package com.example.simple;

public class App {
    public static void main(String[] args) {
        UserService user = UserService.createDefault();
        if (user.validate()) {
            System.out.println(user.getName());
        }
    }
}
