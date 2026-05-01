package com.example.simple;

public class UserService {
    private String name;
    private int age;

    public UserService(String name, int age) {
        this.name = name;
        this.age = age;
    }

    public String getName() {
        return name;
    }

    public boolean validate() {
        return name != null && !name.isEmpty() && age > 0;
    }

    public static UserService createDefault() {
        return new UserService("default", 0);
    }
}
